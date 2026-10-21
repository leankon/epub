"""Consistent local backups: database metadata (JSON lines) + document files + hash manifest.

A backup is only marked `verified` after every file and table export has been re-hashed against
the manifest. Restore refuses unverifiable backups and never restores metadata without its files.
"""

from __future__ import annotations

import datetime as dt
import decimal
import hashlib
import json
import logging
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.types import DateTime

from app.core.config import get_settings
from app.core.errors import AppError, Conflict, NotFound
from app.core.files import ensure_within, sha256_file
from app.db.models import BackupManifest, Base, Job
from app.db.session import get_engine, session_scope
from app.services import audit

log = logging.getLogger("pdfws.backup")
FORMAT_VERSION = 1
FILE_ROOTS = ("documents", "ocr")  # relative to data dir


def _json_default(v: Any) -> Any:
    if isinstance(v, (uuid.UUID, decimal.Decimal)):
        return str(v)
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    raise TypeError(type(v).__name__)


def _hash_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def create_backup(progress: Callable[[str, int, int], None] | None = None) -> dict[str, Any]:
    s = get_settings()
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    bid = uuid.uuid4()
    rel = f"{stamp}-{bid.hex[:8]}"
    bdir = ensure_within(s.backups_dir, s.backups_dir / rel)
    (bdir / "db").mkdir(parents=True)
    bdir.chmod(0o700)
    with session_scope() as sess:
        sess.add(BackupManifest(id=bid, directory=rel, status="running"))
    try:
        manifest: dict[str, Any] = {"format_version": FORMAT_VERSION, "backup_id": str(bid),
                                    "created_at": dt.datetime.now(dt.UTC).isoformat(), "tables": {}, "files": []}
        # One REPEATABLE READ snapshot so all tables are mutually consistent.
        with get_engine().connect() as conn:
            conn = conn.execution_options(isolation_level="REPEATABLE READ")
            with conn.begin():
                rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
                manifest["alembic_revision"] = rev
                tables = Base.metadata.sorted_tables
                for k, t in enumerate(tables):
                    if progress:
                        progress("exporting database", k, len(tables))
                    out = bdir / "db" / f"{t.name}.jsonl"
                    rows = 0
                    with out.open("w", encoding="utf-8") as f:
                        for row in conn.execute(select(t)).mappings():
                            f.write(json.dumps(dict(row), default=_json_default, ensure_ascii=False) + "\n")
                            rows += 1
                    manifest["tables"][t.name] = {"rows": rows, "sha256": sha256_file(out),
                                                  "file": f"db/{t.name}.jsonl"}
                # Copy files referenced at snapshot time (originals/versions are immutable).
                paths = [r[0] for r in conn.execute(text(
                    "SELECT storage_path FROM document_versions WHERE expired_at IS NULL"))]
        files = [("documents", p) for p in paths]
        ocr_root = s.ocr_dir
        if ocr_root.exists():
            files += [("ocr", f.relative_to(ocr_root).as_posix()) for f in ocr_root.rglob("*") if f.is_file()]
        total = 0
        for k, (root, relp) in enumerate(files):
            if progress and k % 25 == 0:
                progress("copying files", k, len(files))
            src = ensure_within(s.data_dir / root, s.data_dir / root / relp)
            if not src.exists():
                raise AppError(f"A registered file is missing: {root}/{relp}", code="backup_missing_file")
            dest = bdir / "files" / root / relp
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            digest = sha256_file(dest)
            size = dest.stat().st_size
            total += size
            manifest["files"].append({"path": f"files/{root}/{relp}", "size": size, "sha256": digest})
        mbytes = json.dumps(manifest, indent=1, ensure_ascii=False).encode("utf-8")
        (bdir / "manifest.json").write_bytes(mbytes)
        msha = _hash_bytes(mbytes)
        (bdir / "manifest.sha256").write_text(f"{msha}  manifest.json\n")
        if progress:
            progress("verifying backup", 0, 1)
        ver = verify_backup_dir(bdir)
        with session_scope() as sess:
            m = sess.get(BackupManifest, bid)
            assert m is not None
            m.file_count, m.total_bytes, m.manifest_sha256 = len(manifest["files"]), total, msha
            m.status = "verified" if ver["ok"] else "failed"
            m.verified_at = dt.datetime.now(dt.UTC) if ver["ok"] else None
            m.error = None if ver["ok"] else json.dumps(ver["problems"][:20])
            audit.record("backup.created", "success" if ver["ok"] else "failure", session=sess,
                         operation="backup", output_sha256=msha,
                         details={"files": len(manifest["files"]), "bytes": total, "verified": ver["ok"],
                                  "directory": rel})
        return {"backup_id": str(bid), "directory": rel, "files": len(manifest["files"]), "bytes": total,
                "manifest_sha256": msha, "verified": ver["ok"], "problems": ver["problems"][:20]}
    except Exception as exc:
        with session_scope() as sess:
            m = sess.get(BackupManifest, bid)
            if m is not None:
                m.status, m.error = "failed", type(exc).__name__
            audit.record("backup.created", "failure", session=sess, operation="backup",
                         details={"error": type(exc).__name__})
        raise


def verify_backup_dir(bdir: Path) -> dict[str, Any]:
    problems: list[str] = []
    mpath = bdir / "manifest.json"
    if not mpath.exists():
        return {"ok": False, "problems": ["manifest.json missing"], "checked": 0}
    mbytes = mpath.read_bytes()
    shafile = bdir / "manifest.sha256"
    if shafile.exists() and shafile.read_text().split()[0] != _hash_bytes(mbytes):
        problems.append("manifest.json does not match manifest.sha256")
    m = json.loads(mbytes)
    checked = 0
    for name, info in m.get("tables", {}).items():
        f = ensure_within(bdir, bdir / info["file"])
        checked += 1
        if not f.exists() or sha256_file(f) != info["sha256"]:
            problems.append(f"table export {name} missing or altered")
    for info in m.get("files", []):
        f = ensure_within(bdir, bdir / info["path"])
        checked += 1
        if not f.exists():
            problems.append(f"missing {info['path']}")
        elif f.stat().st_size != info["size"] or sha256_file(f) != info["sha256"]:
            problems.append(f"hash mismatch {info['path']}")
    return {"ok": not problems, "problems": problems, "checked": checked, "manifest_sha256": _hash_bytes(mbytes)}


def backup_dir_for(backup_id: uuid.UUID) -> Path:
    s = get_settings()
    with session_scope() as sess:
        m = sess.get(BackupManifest, backup_id)
        if m is None:
            raise NotFound("Backup not found.")
        return ensure_within(s.backups_dir, s.backups_dir / m.directory)


def _coerce(col: Any, v: Any) -> Any:
    if v is None:
        return None
    if isinstance(col.type, UUID):
        return uuid.UUID(v)
    if isinstance(col.type, DateTime):
        return dt.datetime.fromisoformat(v)
    if isinstance(col.type, JSONB):
        return v
    return v


def restore_backup(bdir: Path, *, confirm: str, safety_backup: bool = True,
                   admin_url: str | None = None) -> dict[str, Any]:
    """Replace current metadata and files with a verified backup. Previous files are quarantined."""
    from app.services.jobs import manager

    if confirm != "RESTORE":
        raise AppError("Type RESTORE to confirm. Current data will be replaced.", code="confirmation_required")
    ver = verify_backup_dir(bdir)
    if not ver["ok"]:
        raise AppError("Backup failed verification; refusing to restore.", code="backup_unverified",
                       details={"problems": ver["problems"][:20]})
    m = json.loads((bdir / "manifest.json").read_bytes())
    with session_scope() as sess:
        busy = sess.execute(select(Job.id).where(Job.status == "running")).first()
    if busy:
        raise Conflict("Jobs are running. Wait for them to finish or cancel them before restoring.")
    s = get_settings()
    mgr = manager()
    mgr.paused.set()
    safety = None
    try:
        if safety_backup:
            safety = create_backup()
        engine = create_engine(admin_url or s.database_admin_url)
        with engine.begin() as conn:
            rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            if rev != m.get("alembic_revision"):
                raise AppError(f"Backup schema revision {m.get('alembic_revision')} differs from database "
                               f"revision {rev}. Run migrations to the same revision first.", code="schema_mismatch")
            tables = Base.metadata.sorted_tables
            conn.execute(text("ALTER TABLE audit_events DISABLE TRIGGER USER"))
            conn.execute(text("TRUNCATE " + ", ".join(t.name for t in tables) + " CASCADE"))
            for t in tables:
                f = bdir / m["tables"][t.name]["file"]
                batch: list[dict[str, Any]] = []
                cols = {c.name: c for c in t.columns}
                with f.open(encoding="utf-8") as fh:
                    for line in fh:
                        row = json.loads(line)
                        batch.append({k: _coerce(cols[k], v) for k, v in row.items() if k in cols})
                        if len(batch) >= 500:
                            conn.execute(t.insert(), batch)
                            batch = []
                if batch:
                    conn.execute(t.insert(), batch)
            conn.execute(text("ALTER TABLE audit_events ENABLE TRIGGER USER"))
            for tbl in ("audit_events", "job_steps", "ocr_corrections"):  # fixed identifiers, not user input
                conn.execute(text(f"SELECT setval(pg_get_serial_sequence('{tbl}', 'id'), "  # noqa: S608
                                  f"coalesce((SELECT max(id) FROM {tbl}), 0) + 1, false)"))
        engine.dispose()
        stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
        quarantine = s.quarantine_dir / f"pre-restore-{stamp}"
        for root in FILE_ROOTS:
            cur = s.data_dir / root
            if cur.exists():
                quarantine.mkdir(parents=True, exist_ok=True)
                shutil.move(str(cur), str(quarantine / root))
            src = bdir / "files" / root
            if src.exists():
                shutil.copytree(src, cur)
            else:
                cur.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(s.cache_dir / "render", ignore_errors=True)
        from app.services.storage import verify_hashes

        with session_scope() as sess:
            integrity = verify_hashes(sess)
            audit.record("backup.restored", "success" if integrity["ok"] else "warning", session=sess,
                         operation="restore", input_sha256=ver["manifest_sha256"],
                         details={"backup_id": m.get("backup_id"), "files": len(m["files"]),
                                  "integrity_ok": integrity["ok"]})
        return {"restored_from": m.get("backup_id"), "files": len(m["files"]), "integrity": integrity,
                "safety_backup": safety, "previous_files_moved_to": str(quarantine.relative_to(s.data_dir))}
    finally:
        mgr.paused.clear()
